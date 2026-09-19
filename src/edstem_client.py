
from edstem import Client
from env import get_env_variable

EDSTEM_COURSE_ID = int(get_env_variable('COURSE_ID'))

EDSTEM_API_KEY = get_env_variable("EDSTEM_API_KEY")

EDSTEM_EMAIL = get_env_variable("EDSTEM_EMAIL")

EDSTEM_PASSWORD = get_env_variable("EDSTEM_PASSWORD")

ed_client = Client(EDSTEM_API_KEY, authentication="bearer")

