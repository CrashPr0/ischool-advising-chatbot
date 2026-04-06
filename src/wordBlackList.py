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