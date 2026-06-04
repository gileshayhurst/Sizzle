Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; .\venv\Scripts\python.exe -c `"from app import create_app; create_app().run(port=5000, debug=True)`""
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; .\venv\Scripts\python.exe -c `"from generator_app import create_app; create_app().run(port=5001, debug=True)`""
Start-Sleep -Seconds 2
Start-Process "http://localhost:5000"
