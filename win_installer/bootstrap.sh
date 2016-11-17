#!/usr/bin/env bash
# Copyright 2016 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

set -e

function main {
    pacman --noconfirm -Suy

    pacman --noconfirm -S --needed git mingw-w64-i686-gdk-pixbuf2 \
        mingw-w64-i686-librsvg \
        mingw-w64-i686-gtk3 mingw-w64-i686-python2 \
        mingw-w64-i686-python2-gobject \
        mingw-w64-i686-python2-pip \
        mingw-w64-i686-libsoup mingw-w64-i686-gstreamer \
        mingw-w64-i686-gst-plugins-base \
        mingw-w64-i686-gst-plugins-good mingw-w64-i686-libsrtp \
        mingw-w64-i686-gst-plugins-bad mingw-w64-i686-gst-libav \
        mingw-w64-i686-gst-plugins-ugly intltool

    pip install certifi feedparser futures musicbrainzngs mutagen pep8 \
        pyflakes pytest
}

main;
