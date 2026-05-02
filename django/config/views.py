from django.conf import settings
from django.shortcuts import redirect, render

def home(request):
	frontend_url = getattr(settings, 'FRONTEND_DEV_SERVER_URL', '').strip()
	if frontend_url:
		return redirect(frontend_url, permanent=False)
	return render(request, 'index.html')
