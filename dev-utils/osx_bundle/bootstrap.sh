#!/bin/bash

set -e

JHBUILD_REVISION="3.38.0"
RUST_REVISION="1.69.0"

# shellcheck source-path=SCRIPTDIR
source env.sh

# to allow bootstrapping again, try to delete everything first
# shellcheck source-path=SCRIPTDIR
source clean.sh

# Cargo and Rust must be installed in the user's home directory.
# They cannot be run successfully from JHBuild's prefix or home
# directory.
rustup install "$RUST_REVISION"

# Clone and install JHBuild.  Specify "--simple-install" so that we get the same
# behavior regardless of whether autotools is installed or not.
mkdir -p "$HOME" "$QL_OSXBUNDLE_JHBUILD_DEST" "$QL_OSXBUNDLE_BUNDLER_DEST" "$QL_OSXBUNDLE_BUNDLE_DEST"
git clone https://gitlab.gnome.org/GNOME/jhbuild.git "$QL_OSXBUNDLE_JHBUILD_DEST"
(cd "$QL_OSXBUNDLE_JHBUILD_DEST" && git checkout "$JHBUILD_REVISION" && ./autogen.sh --simple-install && make -f Makefile.plain DISABLE_GETTEXT=1 install >/dev/null)
cp misc/gtk-osx-jhbuildrc "$HOME/.jhbuildrc"
cp misc/quodlibet-jhbuildrc-custom "$HOME/.jhbuildrc-custom"

# Clone and install the GNOME Mac bundler.
git clone https://gitlab.gnome.org/GNOME/gtk-mac-bundler.git "$QL_OSXBUNDLE_BUNDLER_DEST"
(cd "$QL_OSXBUNDLE_BUNDLER_DEST" && make install)
