
=========================================================
Tom's memory monitor updated to v3
(based on Matthew Wall's mem demonstration extension)
 - vinceskahan@gmail.com 2014-1129
=========================================================

This example illustrates how to implement a service and package it so that it
can be installed by the extension installer.  The mem service collects memory
usage information about a single process then saves it in its own database.
Data are then displayed using standard weewx reporting and plotting utilities.

Installation instructions:

1) run the installer:

   For weewx v4.10 or later:
       weectl extension install /path/to/mem-extension

   For older weewx versions:
       setup.py install --extension extensions/mem

2) restart weewx:

   Linux (sysvinit):
       sudo /etc/init.d/weewx stop
       sudo /etc/init.d/weewx start

   Linux (systemd):
       sudo systemctl restart weewx

   macOS: restart weewx whichever way it was started (the weewx
   launcher/start script or launchd).

This will result in a skin called mem with a single web page that illustrates
how to use the monitoring data.  See comments in mem.py for customization
options.

Platform support:

The service collects data on both Linux and macOS.

   - Linux reads /proc/<pid>/statm.
   - macOS has no /proc, so the service uses the libproc API instead.
     For that reason the values have a slightly different meaning:

         mem_size  = physical footprint (what Activity Monitor calls "Memory")
         mem_rss   = resident size
         mem_share = resident pages that are not part of the physical
                     footprint (an approximation of shared/clean pages)

     macOS's virtual size is deliberately not used: it includes the dyld
     shared-cache reservation and is typically hundreds of GB, which would
     make the charts useless.

The skin bundles the DejaVuSansMono-Bold font, so the charts look the same on
Linux and macOS without FreeMono having to be installed. To use a different
font, change the *_font_path entries in skins/mem/skin.conf.


Notes:
  1. the 'first' time the skin attempts to generate pages you may see a 
       syslog entry saying 
          "cheetahgenerator: skipping report {'template': 'index.html.tmpl'}: 
               cannot find start time"
     - what is happening is the skin is being processed before the initial
       mem.sdb databased has been seeded with data.  The message goes away
       before the 'second' time the skin is processed.
  2. see bin/user/mem.py for more commentary


