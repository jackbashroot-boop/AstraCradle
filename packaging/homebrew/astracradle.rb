cask "astracradle" do
  version "2.5.0"
  sha256 :no_check

  url "https://github.com/jackbashroot-boop/AstraCradle/releases/download/v#{version}/AstraCradle-macOS.zip"
  name "AstraCradle"
  desc "Cross-platform OS Equilibrium, Network/RF & AstraBeam Tailscale Streaming Studio"
  homepage "https://github.com/jackbashroot-boop/AstraCradle"

  app "AstraCradle.app"
end
