#!/bin/bash

sudo cp -fv synchronizedlyrics.py /usr/lib/python3.9/site-packages/quodlibet/ext/events
sudo chown root /usr/lib/python3.9/site-packages/quodlibet/ext/events/synchronizedlyrics.py
quodlibet
