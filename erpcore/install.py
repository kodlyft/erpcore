# Copyright (c) 2026, Kodlyft and contributors
# For license information, please see license.txt

from erpcore.setup import after_migrate


def after_install():
	after_migrate()
