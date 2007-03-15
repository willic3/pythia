from opal.core import signals
from opal.dispatch import dispatcher
from opal import http
import sys

class BaseHandler(object):
    def __init__(self):
        self._request_middleware = self._view_middleware = self._response_middleware = self._exception_middleware = None

    def load_middleware(self):
        """
        Populate middleware lists from settings.MIDDLEWARE_CLASSES.

        Must be called after the environment is fixed (see __call__).
        """
        from opal.conf import settings
        from opal.core import exceptions
        self._request_middleware = []
        self._view_middleware = []
        self._response_middleware = []
        self._exception_middleware = []
        for middleware_path in settings.MIDDLEWARE_CLASSES:
            try:
                dot = middleware_path.rindex('.')
            except ValueError:
                raise exceptions.ImproperlyConfigured, '%s isn\'t a middleware module' % middleware_path
            mw_module, mw_classname = middleware_path[:dot], middleware_path[dot+1:]
            try:
                mod = __import__(mw_module, '', '', [''])
            except ImportError, e:
                raise exceptions.ImproperlyConfigured, 'Error importing middleware %s: "%s"' % (mw_module, e)
            try:
                mw_class = getattr(mod, mw_classname)
            except AttributeError:
                raise exceptions.ImproperlyConfigured, 'Middleware module "%s" does not define a "%s" class' % (mw_module, mw_classname)

            try:
                mw_instance = mw_class()
            except exceptions.MiddlewareNotUsed:
                continue

            if hasattr(mw_instance, 'process_request'):
                self._request_middleware.append(mw_instance.process_request)
            if hasattr(mw_instance, 'process_view'):
                self._view_middleware.append(mw_instance.process_view)
            if hasattr(mw_instance, 'process_response'):
                self._response_middleware.insert(0, mw_instance.process_response)
            if hasattr(mw_instance, 'process_exception'):
                self._exception_middleware.insert(0, mw_instance.process_exception)

    def get_response(self, path, request):
        "Returns an HttpResponse object for the given HttpRequest"
        from opal.core import exceptions
        from opal.core.mail import mail_admins
        from opal.conf import settings

        # Apply request middleware
        for middleware_method in self._request_middleware:
            response = middleware_method(request)
            if response:
                return response

        try:
            site = settings
            callback, callback_args, callback_kwargs = site.resolve(path)

            # Apply view middleware
            for middleware_method in self._view_middleware:
                response = middleware_method(request, callback, callback_args, callback_kwargs)
                if response:
                    return response

            try:
                response = callback(request, *callback_args, **callback_kwargs)
            except Exception, e:
                # If the view raised an exception, run it through exception
                # middleware, and if the exception middleware returns a
                # response, use that. Otherwise, reraise the exception.
                for middleware_method in self._exception_middleware:
                    response = middleware_method(request, e)
                    if response:
                        return response
                raise

            # Complain if the view returned None (a common error).
            if response is None:
                raise ValueError, "The view %s.%s didn't return an HttpResponse object." % (callback.__module__, callback.func_name)

            return response
        except http.Http404, e:
            if settings.DEBUG:
                return self.get_technical_error_response(request, is404=True, exception=e)
            else:
                callback, param_dict = resolver.resolve404()
                return callback(request, **param_dict)
        except exceptions.PermissionDenied:
            return http.HttpResponseForbidden('<h1>Permission denied</h1>')
        except SystemExit:
            pass # See http://code.djangoproject.com/ticket/1023
        except: # Handle everything else, including SuspiciousOperation, etc.
            if settings.DEBUG:
                return self.get_technical_error_response(request)
            else:
                # Get the exception info now, in case another exception is thrown later.
                exc_info = sys.exc_info()
                receivers = dispatcher.send(signal=signals.got_request_exception)
                # When DEBUG is False, send an error message to the admins.
                subject = 'Error (%s IP): %s' % ((request.META.get('REMOTE_ADDR') in settings.INTERNAL_IPS and 'internal' or 'EXTERNAL'), getattr(request, 'path', ''))
                try:
                    request_repr = repr(request)
                except:
                    request_repr = "Request repr() unavailable"
                message = "%s\n\n%s" % (self._get_traceback(exc_info), request_repr)
                mail_admins(subject, message, fail_silently=True)
                return self.get_friendly_error_response(request, resolver)

    def get_friendly_error_response(self, request, resolver):
        """
        Returns an HttpResponse that displays a PUBLIC error message for a
        fundamental error.
        """
        callback, param_dict = resolver.resolve500()
        return callback(request, **param_dict)

    def get_technical_error_response(self, request, is404=False, exception=None):
        """
        Returns an HttpResponse that displays a TECHNICAL error message for a
        fundamental error.
        """
        from opal.views import debug
        if is404:
            return debug.technical_404_response(request, exception)
        else:
            return debug.technical_500_response(request, *sys.exc_info())

    def _get_traceback(self, exc_info=None):
        "Helper function to return the traceback as a string"
        import traceback
        return '\n'.join(traceback.format_exception(*(exc_info or sys.exc_info())))