def read_cups_bottom():
    cups_order = []
    user_input = input("Pepper: Please read the colors of the cups from left to right, separated by commas (e.g., RGBY): ")
    cups_order = list(user_input.strip().upper())  # Convert to uppercase and split into a list of characters
    return cups_order

def read_cups_top():
    cups_order = []
    user_input = input("Pepper: Please read the colors of the cups from left to right, separated by commas (e.g., RGBY): ")
    cups_order = list(user_input.strip().upper())  # Convert to uppercase and split into a list of characters
    return cups_order

def read_all_cups():
    bottom_cups = read_cups_bottom()
    top_cups = read_cups_top()
    print("Pepper sees the following cups:")
    print("Top row:", top_cups)
    print("Bottom row:", bottom_cups)
    return bottom_cups, top_cups