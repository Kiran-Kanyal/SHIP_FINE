from rich.console import Console
from rich.spinner import Spinner
from rich.live import Live
import time

console = Console()

def run_with_spinner(message, func, *args, **kwargs):
    spinner = Spinner("dots", text=message)
    with Live(spinner, refresh_per_second=12):
        result = func(*args, **kwargs)
        time.sleep(0.5)
    console.print(f"[green]✔ {message}[/green]")
    return result