# -*- coding: utf-8 -*-
"""UI bus notifications for Seerbit payment events (backend toast + soft reload)."""


def send_seerbit_ui_notification(env, title, message, record=None):
    """Broadcast a ``seerbit_payment_received`` bus event.

    Frontend only shows toasts for ``group_seerbit_notification_user``.
    Soft-reload runs only when the open form matches ``record``.
    """
    payload = {
        'title': title,
        'message': message,
    }
    if record is not None:
        payload['res_model'] = record._name
        payload['res_id'] = record.id
    env['bus.bus'].sudo()._sendone('broadcast', 'seerbit_payment_received', payload)
