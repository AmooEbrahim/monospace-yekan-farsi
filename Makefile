PY ?= python3

REG  = dist/MonospaceYekanFarsi-Regular.ttf
BOLD = dist/MonospaceYekanFarsi-Bold.ttf

.PHONY: all check specimen clean

all: $(REG) $(BOLD)

$(REG): sources/Far_Yekan.ttf sources/UbuntuSansMono[wght].ttf tools/build.py
	$(PY) tools/build.py --weight 400 --out $@

$(BOLD): sources/Far_Yekan.ttf sources/UbuntuSansMono[wght].ttf tools/build.py
	$(PY) tools/build.py --weight 700 --out $@

check: all
	$(PY) tests/qa.py $(REG)
	$(PY) tests/qa.py $(BOLD)

specimen: all
	$(PY) tools/specimen.py

clean:
	rm -f $(REG) $(BOLD)
