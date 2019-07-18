all: build install

build:
       python3 setup.py build

install:
       python3 setup.py install --user
       cp textlineerkenner/textlineerkenner.py ~/bin/textlineerkenner
       chmod +x ~/bin/textlineerkenner
