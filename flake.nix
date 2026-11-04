{
  description = "Local Python runtime for the math-worksheet mod";

  inputs.nixpkgs.url = "nixpkgs";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      packages.${system} = {
        # What the worksheet server and the grader run on.
        default = pkgs.python3.withPackages (p: [ p.sympy p.mpmath p.pytest ]);
        # Only for the browser tests in tests/test_browser.py.
        browserTests = pkgs.python3.withPackages (p: [ p.sympy p.mpmath p.pytest p.playwright ]);
        playwrightBrowsers = pkgs.playwright-driver.browsers;
      };
    };
}
