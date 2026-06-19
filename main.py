from utilities.fs import view
from downloader import run

print(view('./assets/intromessage.txt')) # Loads intro message kinda self explanatory

url = input("  Enter Truffled game URL: ").strip()
run(url)