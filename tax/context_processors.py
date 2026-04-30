from .models import TaxConfiguration


def tax_config(request):
    return {'tax_config': TaxConfiguration.load()}
