from codroid_api import load_capture


def main() -> None:
    capture = load_capture("basics.har")
    counts = capture.action_counts(direction="send")

    print("Captured send actions:")
    for action, count in counts.most_common():
        print(f"- {action}: {count}")


if __name__ == "__main__":
    main()
