"""Scheduled historical admin reporting; never fits a model or changes forecasts."""
import json

from . import fwd, pipeline


def main():
    fwd.require()
    print(json.dumps(pipeline.write_reports()), flush=True)


if __name__ == "__main__":
    main()
