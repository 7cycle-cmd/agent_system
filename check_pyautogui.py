"""Check if pyautogui is installed."""
try:
    import pyautogui
    print('pyautogui installed at:', pyautogui.__file__)
    print('version:', getattr(pyautogui, '__version__', 'unknown'))
except ImportError as e:
    print('pyautogui NOT installed:', e)
