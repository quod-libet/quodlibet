{
  description = "Development Flake for Quod Libet";
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-23.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
  }:
    flake-utils.lib.eachDefaultSystem (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      qlPython = pkgs.python310;
      qlPoetry = pkgs.poetry.override {python3 = qlPython;};
    in {
      # A shell for `nix develop`
      devShells.default = pkgs.mkShell {
        packages = [
          qlPoetry
          qlPython
          pkgs.shellcheck
          pkgs.alejandra
          pkgs.cairo
          pkgs.ninja
          pkgs.pkg-config
          pkgs.gobject-introspection
          pkgs.gdk-pixbuf
          pkgs.gtk3
          pkgs.libsoup_3
        ];
      };

      # Allow `nix fmt` to "just work"
      formatter = pkgs.alejandra;

      # Nix run .#foo
      apps = {
        poetry = flake-utils.lib.mkApp {drv = qlPoetry;};
      };

      # Allow `nix flake check` to "just work"
      checks = {
        shellcheck =
          pkgs.runCommand
          "shellcheck"
          {buildInputs = [pkgs.shellcheck];}
          ''
            find ${./.} -iname "*.sh" -exec shellcheck {} \+
            # We *must* create some output
            mkdir -p "$out"
          '';
      };
    });
}
