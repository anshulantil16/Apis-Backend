class AppSourceMiddleware:
    """
    Injects request.app_source = 'performance' or 'appraisal'
    based on whether the request path starts with /api/appraisal/.
    All performance views filter their DB queries by this value.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.app_source = 'appraisal' if request.path.startswith('/api/appraisal/') else 'performance'
        return self.get_response(request)
