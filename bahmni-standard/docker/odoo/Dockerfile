ARG ODOO_IMAGE_TAG=1.0.0
FROM bahmni/odoo-16:${ODOO_IMAGE_TAG}

USER root
RUN /usr/bin/python3 -m pip install --no-cache-dir openpyxl==3.1.5
USER odoo
