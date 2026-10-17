"""Loop del worker (spec 08). Por ahora solo el gancho que usa el lifespan."""

from collections.abc import Callable


def start_worker() -> Callable[[], None]:
    """Arranca el hilo del worker y devuelve la función que lo detiene.

    TODO(spec 08): hilo daemon que llama `run_once(db, get_op_client(), clock.now(), reconcile=due)` cada
    `WORKER_INTERVAL_SECONDS` y se detiene con un `threading.Event`.
    """
    return lambda: None
