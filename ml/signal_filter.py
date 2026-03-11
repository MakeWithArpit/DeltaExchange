"""
ML Signal Filter — Random Forest Classifier
Trains on historical BTC/ETH/SOL data to predict signal quality
Adds extra confidence layer on top of BB Squeeze strategy
"""
import numpy as np
import pandas as pd
import pickle, os, logging
from datetime import datetime
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import classification_report
from sklearn.pipeline import Pipeline

logger = logging.getLogger(__name__)

MODEL_PATH = "data/ml_model.pkl"
FEATURE_PATH = "data/ml_features.pkl"


class MLSignalFilter:
    """
    Random Forest that predicts: will this BB Squeeze signal WIN or LOSE?
    
    Features used:
    - squeeze_duration     (longer squeeze = better)
    - breakout_strength    (stronger break = better)
    - volume_ratio         (higher volume = better)
    - macd_histogram       (momentum strength)
    - atr_normalized       (volatility context)
    - hour_of_day          (time-of-day effect)
    - day_of_week          (weekday effect)
    - trend_strength_4h    (4H EMA distance)
    - recent_win_rate      (last 10 signals)
    - bb_width_percentile  (how compressed before break)
    """

    def __init__(self):
        self.model   = None
        self.scaler  = None
        self.trained = False
        self.n_features = 10

    def _build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Build ML feature matrix from indicator dataframe"""
        feat = pd.DataFrame(index=df.index)

        atr_safe = df["atr"].replace(0, np.nan).ffill().bfill()

        feat["squeeze_duration"]  = df.get("squeeze_dur", 0).fillna(0)
        feat["breakout_strength"] = df.get("breakout_str",
                                     np.where(df["close"]>df["bb_up"],
                                     (df["close"]-df["bb_up"])/atr_safe,
                                     np.where(df["close"]<df["bb_lo"],
                                     (df["bb_lo"]-df["close"])/atr_safe, 0))).fillna(0)
        feat["volume_ratio"]      = df.get("vol_ratio", 1.0).fillna(1.0).clip(0, 10)
        feat["macd_histogram"]    = df.get("macd_hist",
                                     df.get("macd",0) - df.get("macd_sig",0)).fillna(0)
        feat["atr_normalized"]    = (atr_safe / df["close"]).fillna(0.01)
        feat["hour_sin"]          = np.sin(2*np.pi * df["time"].dt.hour / 24)
        feat["hour_cos"]          = np.cos(2*np.pi * df["time"].dt.hour / 24)
        feat["day_of_week"]       = df["time"].dt.dayofweek / 6.0
        feat["bb_width_pct"]      = df.get("bb_width_pct",
                                     df["bb_width"].rolling(100).rank(pct=True)).fillna(0.5)
        feat["trend_strength"]    = (df.get("trend_4h", 0) *
                                     abs(df["close"] - df.get("ema_4h",
                                         df["close"].ewm(span=21).mean())) /
                                     atr_safe).fillna(0)
        return feat

    def _collect_training_data(self, df: pd.DataFrame,
                                signal_col_l: pd.Series,
                                signal_col_s: pd.Series) -> tuple:
        """
        Collect (features, label) for each signal
        label = 1 if trade won (hit TP), 0 if lost (hit SL)
        """
        from config.settings import ATR_MULTIPLIER, RR_RATIO
        df = df.copy().reset_index(drop=True)

        atr_safe = df["atr"].replace(0, np.nan).ffill().bfill()
        feat_df  = self._build_features(df)

        X_list = []; y_list = []
        in_trade = False; direction = 0; entry = sl = tp = 0.0; eb = -1; sig_feat = None

        for i in range(250, len(df)-5):
            row = df.iloc[i]

            if in_trade and i > eb:
                h = row["high"]; l = row["low"]
                sl_hit = (l<=sl) if direction==1 else (h>=sl)
                tp_hit = (h>=tp) if direction==1 else (l<=tp)
                if sl_hit or tp_hit:
                    label = 1 if (tp_hit and not sl_hit) else 0
                    X_list.append(sig_feat); y_list.append(label)
                    in_trade = False

            if not in_trade:
                atr = float(atr_safe.iloc[i])
                if atr == 0: continue
                is_l = bool(signal_col_l.iloc[i])
                is_s = bool(signal_col_s.iloc[i])
                if is_l or is_s:
                    price = float(row["close"])
                    if is_l:
                        sl = price - atr * ATR_MULTIPLIER
                        tp = price + abs(price-sl) * RR_RATIO; direction = 1
                    else:
                        sl = price + atr * ATR_MULTIPLIER
                        tp = price - abs(price-sl) * RR_RATIO; direction = -1
                    entry = price; in_trade = True; eb = i
                    sig_feat = feat_df.iloc[i].values.tolist()

        return np.array(X_list), np.array(y_list)

    def train(self, datasets: dict):
        """
        Train model on historical data
        datasets: {"BTC": df_btc, "ETH": df_eth, "SOL": df_sol}
        Each df must have indicators already computed
        """
        from core.strategy import StrategyEngine
        engine = StrategyEngine()
        all_X = []; all_y = []

        for name, df in datasets.items():
            logger.info(f"Collecting training data from {name}...")
            df = engine.prepare_indicators(df)

            sig_l = (df["bb_squeeze"].shift(1) &
                     (df["close"] > df["bb_up"].shift(1)) &
                     (df["trend_4h"] == 1) & df["macd_bull"] &
                     (~df["is_weekend"]))
            sig_s = (df["bb_squeeze"].shift(1) &
                     (df["close"] < df["bb_lo"].shift(1)) &
                     (df["trend_4h"] == -1) & (~df["macd_bull"]) &
                     (~df["is_weekend"]))

            X, y = self._collect_training_data(df, sig_l, sig_s)
            if len(X) > 0:
                all_X.append(X); all_y.append(y)
                logger.info(f"  {name}: {len(X)} samples, WR={y.mean()*100:.1f}%")

        if not all_X:
            logger.error("No training data collected!")
            return False

        X_all = np.vstack(all_X); y_all = np.concatenate(all_y)
        logger.info(f"\nTotal samples: {len(X_all)}, WR: {y_all.mean()*100:.1f}%")

        # Handle NaN/Inf
        X_all = np.nan_to_num(X_all, nan=0.0, posinf=1.0, neginf=-1.0)

        # Time-series split (no data leakage)
        tscv = TimeSeriesSplit(n_splits=3)

        best_model = None; best_score = 0
        for fold, (train_idx, val_idx) in enumerate(tscv.split(X_all)):
            X_tr, X_val = X_all[train_idx], X_all[val_idx]
            y_tr, y_val = y_all[train_idx], y_all[val_idx]

            # Random Forest
            rf = Pipeline([
                ("scaler", StandardScaler()),
                ("clf", RandomForestClassifier(
                    n_estimators=200,
                    max_depth=6,
                    min_samples_leaf=20,
                    class_weight="balanced",
                    random_state=42,
                    n_jobs=-1
                ))
            ])
            rf.fit(X_tr, y_tr)
            score = rf.score(X_val, y_val)
            if score > best_score:
                best_score = score; best_model = rf
            logger.info(f"  Fold {fold+1}: accuracy={score:.3f}")

        self.model   = best_model
        self.trained = True

        # Evaluate on full data
        preds = best_model.predict(X_all)
        logger.info(f"\nFinal Model Report:")
        logger.info(classification_report(y_all, preds, target_names=["Loss","Win"]))

        # Feature importance
        rf_clf = best_model.named_steps["clf"]
        feat_names = ["squeeze_dur","breakout_str","vol_ratio","macd_hist",
                      "atr_norm","hour_sin","hour_cos","dow","bb_width_pct","trend_str"]
        importances = rf_clf.feature_importances_
        logger.info("\nFeature Importances:")
        for n, imp in sorted(zip(feat_names, importances), key=lambda x:-x[1]):
            bar = "█" * int(imp*50)
            logger.info(f"  {n:<20}: {imp:.4f}  {bar}")

        # Save model
        os.makedirs("data", exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump({"model": self.model, "trained_at": datetime.now()}, f)
        logger.info(f"Model saved → {MODEL_PATH}")
        return True

    def load(self) -> bool:
        """Load pre-trained model"""
        if os.path.exists(MODEL_PATH):
            with open(MODEL_PATH, "rb") as f:
                data = pickle.load(f)
                self.model   = data["model"]
                self.trained = True
                logger.info(f"Model loaded (trained: {data.get('trained_at','')})")
                return True
        return False

    def predict(self, features: dict) -> dict:
        """
        Predict win probability for a signal
        features: dict with indicator values at signal time
        Returns: {"win_prob": float, "confidence": float, "take_trade": bool}
        """
        if not self.trained or self.model is None:
            return {"win_prob": 0.5, "confidence": 0.5, "take_trade": True,
                    "reason": "Model not trained — using default"}

        feat_vec = np.array([[
            features.get("squeeze_duration", 0),
            features.get("breakout_strength", 0),
            features.get("volume_ratio", 1.0),
            features.get("macd_histogram", 0),
            features.get("atr_normalized", 0.01),
            features.get("hour_sin", 0),
            features.get("hour_cos", 1),
            features.get("day_of_week", 0),
            features.get("bb_width_pct", 0.5),
            features.get("trend_strength", 0),
        ]])
        feat_vec = np.nan_to_num(feat_vec, nan=0.0)

        try:
            proba     = self.model.predict_proba(feat_vec)[0]
            win_prob  = float(proba[1]) if len(proba) > 1 else 0.5
            from config.settings import ML_MIN_CONFIDENCE
            take_trade = win_prob >= ML_MIN_CONFIDENCE
            return {
                "win_prob":   round(win_prob, 4),
                "confidence": round(win_prob, 4),
                "take_trade": take_trade,
                "reason":     f"ML: {win_prob*100:.1f}% win probability"
                              f" ({'TAKE' if take_trade else 'SKIP'})"
            }
        except Exception as e:
            logger.error(f"ML prediction error: {e}")
            return {"win_prob": 0.5, "confidence": 0.5, "take_trade": True,
                    "reason": f"ML error: {e}"}