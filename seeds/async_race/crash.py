"""Reproduce the late-binding closure bug.

Two configs with different schemas: the first has a 'name', the second has
'billing_id'. After make_handlers runs, all handlers late-bind to the last
config. Calling handlers[0]('login') crashes with KeyError because the
closure is actually reading `configs[1]`, which has no 'name' key.
"""

from src.workers.dispatcher import make_handlers


def main() -> None:
    configs = [{"name": "auth"}, {"billing_id": 42}]
    handlers = make_handlers(configs)
    result = handlers[0]("login")
    print("ok:", result)


if __name__ == "__main__":
    main()
