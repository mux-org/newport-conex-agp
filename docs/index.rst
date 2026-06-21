newport-conex-agp
=================
This container provides a REST API to control `Newport CONEX-AGP
<https://www.newport.com/f/agilis-piezo-linear-stage-with-conex-controller>`_
linear stages. 

Configure
---------

config.yaml
~~~~~~~~~~~
A sample config file is provided below:

.. code:: yaml

   device: /dev/newport-stage
   address: 1
   label: stage         # human readable label
   auto_home: false     # home the stage on startup?
   travel: [0.0, 27.0]  # travel limits
   units: mm            # display metadata
   poll_interval: 0.25  # seconds between status polls while ?wait is blocking
   default_timeout: 30  # max seconds a ?wait blocks (and homing budget)

A software emulator is also provided. To use the emulator, use ``emulator://``
as the device. Additional emulation parameters are also available:

.. code:: yaml

   device: emulator://
   speed: 5.0           # emulated units/second
   home_position: 0.0   # emulated HOME position
   home_time: 1.5       # emulated homing duration

udev rules
~~~~~~~~~~

A udev rules file should be created for each device to ensure it is always
mounted at a known and repeatable path. The sample rule provided below will
mount the stage with serial ``XXXXXXXX`` at ``/dev/newport-stage``:

.. code::

   # /etc/udev/rules.d/99-newport-stage.rules

   SUBSYSTEM=="tty", \
      ATTRS{idVendor}=="104d", \
      ATTRS{idProduct}=="3006", \
      ATTRS{serial}=="XXXXXXXX", \
      MODE:="0666", \
      SYMLINK+="newport-stage"


Run
---
The container expects both a config file and host device to run:

.. code:: bash

   podman run --name newport-stage \
      -v /path/to/config.yaml:/app/config.yaml \
      --device /dev/newport-stage:/dev/newport-stage \
      localhost/newport_conex_agp:latest

The following environment variables can be used to configure the container at
runtime:

.. envvar:: HOST

   REST API endpoint IP address. Default is ``0.0.0.0``

.. envvar:: PORT

   REST API endpoint port. Default is ``8000``

.. envvar:: API_KEY

   Optional API key for simple auth. Auth is disabled if ``API_KEY`` is unset (default)

.. envvar:: LOG_LEVEL

   Logging level. Default is ``info``

API
---
.. openapi:: openapi.yaml
