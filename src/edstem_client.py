
from edstem import Client
from env import get_env_variable

EDSTEM_AUTH_TOKEN = get_env_variable('EDSTEM_AUTH_TOKEN')

EDSTEM_COURSE_ID = int(get_env_variable('COURSE_ID'))

EDSTEM_API_KEY = get_env_variable("EDSTEM_API_KEY")

ed_client = Client(EDSTEM_API_KEY, authentication="bearer")

