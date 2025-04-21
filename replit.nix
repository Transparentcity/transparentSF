{ pkgs }: {
  deps = [
    pkgs.python311Full
    pkgs.postgresql_16
    pkgs.zlib
    pkgs.glibcLocales
    pkgs.libiconv
    pkgs.cargo
    pkgs.geckodriver
    pkgs.rustc
    pkgs.qdrant
  ];
}
