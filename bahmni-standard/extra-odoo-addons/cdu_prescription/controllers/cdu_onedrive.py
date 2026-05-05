from werkzeug import urls

from odoo import http
from odoo.http import request


class CduOneDriveController(http.Controller):
    @http.route("/cdu/onedrive/callback", type="http", auth="user", website=False)
    def onedrive_callback(self, code=None, state=None, error=None, error_description=None, **kwargs):
        source = request.env["cdu.onedrive.source"].sudo().search([
            ("authorization_state", "=", state or ""),
        ], limit=1)
        if not source:
            return request.redirect("/web")

        try:
            if error:
                source.write({
                    "last_poll_result": error_description or error,
                    "authorization_state": False,
                })
            elif code:
                source._exchange_authorization_code(code, state)
                source.write({
                    "last_poll_result": "OneDrive account connected successfully.",
                })
        except Exception as exc:
            source.write({
                "last_poll_result": str(exc),
                "authorization_state": False,
            })

        hash_params = {
            "id": source.id,
            "model": "cdu.onedrive.source",
            "view_type": "form",
        }
        return request.redirect("/web#%s" % urls.url_encode(hash_params))
