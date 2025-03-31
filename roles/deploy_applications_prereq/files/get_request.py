import sys

def get_value(number):
    if number % 8 in {1, 2, 3}:
        return "140m"
    elif number % 8 in {4, 5, 6, 7, 0}:
        return "28m"
    else:
        return "Invalid number"

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python get_request.py <number>")
        sys.exit(1)
    
    try:
        number = int(sys.argv[1])
        print(get_value(number))
    except ValueError:
        print("Please provide a valid integer.")
        sys.exit(1)