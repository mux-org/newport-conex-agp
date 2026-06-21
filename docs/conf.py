from muxdoc.util import dump_openapi

project = 'newport-conex-agp'
author = 'Andy Kee'
copyright = '%Y, California Institute of Technology'
extensions = ['muxdoc']

dump_openapi(app='stage_api.app:app', app_dir='../src')
