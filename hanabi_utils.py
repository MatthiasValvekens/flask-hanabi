
CARD_DIST_PER_COLOUR = [3, 2, 2, 2, 1]

COLOUR_NAMES = [
    'red', 'yellow', 'green', 'blue', 'white'
]


def hanabi_colour(num: int) -> str:
    try:
        return COLOUR_NAMES[num]
    except IndexError:
        return f'col{num}'
