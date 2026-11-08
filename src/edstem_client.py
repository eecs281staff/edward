
from edstem import Client
from env import settings

ed_client = Client(settings.EDSTEM_API_KEY, authentication="bearer")

