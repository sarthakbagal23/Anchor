@echo off
cd /d "C:\Users\sarth\Desktop\opennotebook"
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8765 >> "C:\Users\sarth\Desktop\opennotebook\server-out.log" 2>> "C:\Users\sarth\Desktop\opennotebook\server-err.log"
