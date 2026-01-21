import sys

def get_value(name):
    if name.lower().startswith("high"):
        return "1"
    else:
        return "250m"
    
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python get_request.py <number>")
        sys.exit(1)
    
    number = sys.argv[1]
    print(get_value(number))
    