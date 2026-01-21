import sys

def get_value(name):
    if name.lower().startswith("high"):
        return "150m" # 200m is 1cpu / 6.6666
    else:
        return "30m" # 40m is 0.2cpu / 8

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python get_request.py <number>")
        sys.exit(1)
    number = sys.argv[1]
    print(get_value(number))   