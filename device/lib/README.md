# Вендоренные библиотеки

## `ssd1306.py`

- Источник: micropython-lib, `micropython/drivers/display/ssd1306/ssd1306.py`
- URL: https://github.com/micropython/micropython-lib
- Лицензия: MIT (Copyright (c) 2016 Damien P. George и участники micropython-lib)
- Файл скопирован без изменений.

Драйвер поддерживает и 128×64, и 128×32: COM-пины выбираются автоматически
по `width > 2 * height`, MUX ratio берётся из `height`. На плате файл лежит
в корне (`:ssd1306.py`), потому что файловая система MicroPython плоская:

```bash
venv/bin/mpremote connect /dev/ttyUSB0 fs cp device/lib/ssd1306.py :ssd1306.py
```
