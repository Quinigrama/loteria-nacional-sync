@echo off
REM ============================================================
REM Actualizar Loteria Nacional - lanzador para Task Scheduler
REM ============================================================
REM Este .bat hace 3 cosas:
REM   1. Se asegura de estar en la carpeta correcta del script
REM      (importante para Task Scheduler, que a veces arranca en
REM      una carpeta distinta a la que esperamos).
REM   2. Ejecuta el script de Python.
REM   3. Guarda toda la salida (normal y errores) en un archivo
REM      de log con fecha, para poder comprobar despues si la
REM      tarea funciono sin depender del Historial de Windows.

cd /d "C:\Users\Roberto\Desktop\LoteriaNacional"

echo ============================================== >> log_ejecucion.txt
echo Ejecucion: %date% %time% >> log_ejecucion.txt
echo ============================================== >> log_ejecucion.txt

C:\Python313\python.exe loteria_nacional_sync.py >> log_ejecucion.txt 2>&1

echo. >> log_ejecucion.txt
echo Terminado: %date% %time% >> log_ejecucion.txt
echo. >> log_ejecucion.txt
