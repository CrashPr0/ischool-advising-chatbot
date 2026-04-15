import os

def load_keywords(file_path: str):
	with open(file_path, "r", encoding="utf-8")as f:
		return [line.strip() for line in f if line.strip()]

def search_words(text: str, keywords: list):
	words = text.split()
	matches = []
	for word in words:
		for keyword in keywords:
			if keyword.lower() in word.lower():
				matches.append(word)
				break
	return matches

def print_if_blacklist(text: str, keywords: list):
	results = search_words(text, keywords);
	if results and "myself" in text:
		print("\nAssistant: Error! Request can not be made due to containing an inappropriate word!")
		print(f"[Inappropriate words: {', '.join(results)}]")
		print(f"\nDetected that response may releate to self-harm!")
		print(f"\nIf you or someone you know are in need of help, you can refer to SJSU's Behavioral Intervention site(https://www.sjsu.edu/bit/) for help")
		print()
		return 1;
	elif results:
		print("\nAssistant: Error! Request can not be made due to containing an inappropriate word!")
		print(f"[Inappropriate words: {', '.join(results)}]")
		print(f"\nPlease use academic/appropriate language!")
		print()
		return 1;		
