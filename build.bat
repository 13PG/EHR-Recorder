@echo off
echo ========================================
echo  T9110 CloudMap - Build EXE
echo ========================================
echo.

echo [1/2] Installing dependencies...
pip install pymodbus pyserial numpy matplotlib scipy openpyxl pyinstaller
echo.

echo [2/2] Building EXE...
pyinstaller --onefile --windowed --name T9110_CloudMap ^
    --hidden-import=pymodbus.client ^
    --hidden-import=pymodbus.framer ^
    --hidden-import=scipy.interpolate ^
    --hidden-import=matplotlib.backends.backend_tkagg ^
    --hidden-import=openpyxl ^
    t9110_cloudmap.py
echo.

echo ========================================
if exist dist\T9110_CloudMap.exe (
    echo  Build successful!
    echo  EXE: dist\T9110_CloudMap.exe
) else (
    echo  Build failed. Check errors above.
)
echo ========================================
pause
