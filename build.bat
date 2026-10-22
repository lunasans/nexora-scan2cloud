@echo off
REM Baut Scan2Cloud als 32-Bit-Programm (noetig fuer den 32-Bit-TWAIN-Treiber von Canon).
REM Voraussetzung: Python 3.12 in der 32-Bit-Version ("Windows installer (32-bit)" von python.org).
setlocal
py -3.12-32 -c "import sys; assert sys.maxsize < 2**32" || (
  echo 32-Bit-Python 3.12 nicht gefunden. Bitte von python.org installieren.
  exit /b 1
)
if not exist .venv32 py -3.12-32 -m venv .venv32
call .venv32\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -e .[dev] || exit /b 1
pyinstaller --noconfirm --windowed --name Scan2Cloud ^
  --collect-data sv_ttk ^
  --hidden-import keyring.backends.Windows ^
  scan2cloud\__main__.py || exit /b 1
echo.
echo Fertig: dist\Scan2Cloud\Scan2Cloud.exe
