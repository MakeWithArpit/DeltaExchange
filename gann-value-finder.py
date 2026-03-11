import math as m

def gann(value, upto=10, toggle=1):
    valueList = []
    toggle_bool = bool(toggle)

    number = int(value)
    squareRoot = int(m.sqrt(number))

    for _ in range(upto):
        square = squareRoot ** 2

        if square % 2 == 0:
            valueList.append(square + 1)
        else:
            valueList.append(square)

        if toggle_bool:
            squareRoot += 1
        else:
            squareRoot -= 1

    return valueList

result = gann(value=70810, upto=11, toggle=0)
print(result)