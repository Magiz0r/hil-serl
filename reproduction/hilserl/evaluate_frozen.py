"""Evaluate a frozen HIL-SERL policy without human or automatic assistance."""
from reproduction.autoserl.evaluate_frozen import main

if __name__ == '__main__':
    main(algorithm='hilserl')
