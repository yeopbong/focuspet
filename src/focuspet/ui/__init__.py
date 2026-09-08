"""Native Qt desktop companion. Domain and persistence remain outside this package."""


def run_app(mode="real", data_dir=None, service=None, **kwargs):
    from .app import run_app as launch

    return launch(mode=mode, data_dir=data_dir, service=service, **kwargs)
