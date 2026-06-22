from firebase_functions import https_fn, options

@https_fn.on_request(
    memory=options.MemoryOption.GB_4,  # 4 GB → unlocks 2 vCPUs (vs 1 vCPU at 1-2 GB)
    timeout_sec=1200,                      # 20 min ceiling; accommodates long LLM debates
    min_instances=0,                       # No idle instances — scale to zero when not in use
    cpu=2,                                 # Explicit 2 vCPU (aligned with 4096 MB tier)
    concurrency=1,                         # Each analysis blocks the worker; 1 req/instance
    ingress=options.IngressSetting.ALLOW_ALL,
)
def api(req: https_fn.Request) -> https_fn.Response:
    """Wrap and run Flask app serverlessly on Firebase Cloud Functions."""
    from app import app as flask_app
    with flask_app.request_context(req.environ):
        return flask_app.full_dispatch_request()
