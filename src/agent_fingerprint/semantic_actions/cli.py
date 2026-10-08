"""Compatibility for the former semantic CLI signature."""
def main(format_l3=None, argv=None):
    from agent_fingerprint.cli.extract import main as extract
    return extract(argv)
