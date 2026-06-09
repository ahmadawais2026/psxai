from firebase_functions import https_fn

@https_fn.on_request(memory=1024, timeout_sec=300)
def api(req: https_fn.Request) -> https_fn.Response:
    """Wrap and run Flask app serverlessly on Firebase Cloud Functions."""
    from app import app as flask_app
    with flask_app.request_context(req.environ):
        return flask_app.full_dispatch_request()

