# SPDX-License-Identifier: GPL-3.0-or-later
"""AI UV Layout — KI-UV-Unwrapping ueber die Scenario API (Modelle in models.json).

Port des UV-Layout-Schritts aus der Phototron Desktop-App als Blender-Add-on.
Das aktive Mesh wird im Object Mode als OBJ hochgeladen, vom Modell mit UVs
versehen, und die UV-Koordinaten kommen als neue, durchnummerierte UV-Map
(AI_UV_1, AI_UV_2, ...) auf das Original. Vorhandene UV-Maps bleiben.
"""

from . import preferences, props, history, operators, panel

# history nach props: sein register() spiegelt die Historie in die Liste, die
# props am WindowManager anlegt
_modules = (preferences, props, history, operators, panel)


def register():
    for mod in _modules:
        mod.register()


def unregister():
    for mod in reversed(_modules):
        mod.unregister()
