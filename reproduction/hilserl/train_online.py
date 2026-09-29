"""Prepare attended HIL-SERL training, paused until the operator starts it."""
from reproduction.autoserl.train_online import main

if __name__ == '__main__':
    main(algorithm='hilserl')
