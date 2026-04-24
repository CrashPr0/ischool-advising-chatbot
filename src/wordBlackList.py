import os

def load_keywords(file_path: str):#load words from file i.e. blacklisted words
	with open(file_path, "r", encoding="utf-8")as f:
		return [line.strip() for line in f if line.strip()]

def search_words(text: str, keywords: list):#searches within given string and returns a list of matches i.e. if a blacklisted word was inputted
	words = text.split()
	matches = []
	for word in words:
		for keyword in keywords:
			if keyword.lower() in word.lower():
				matches.append(word)
				break
	return matches

def print_if_blacklist(text: str, keywords: list):#prints a msg according to what was given, if myself was detected then it sends a different msg
	results = search_words(text, keywords);
	if results and "myself" or "suicide" in text:
		print("\nSorry, I can not answer this because you used an inappropriate word!")
		#print(f"[Inappropriate words: {', '.join(results)}]") keeping these commented just in case it is needed in the future
		#print(f"\nDetected that response may releate to self-harm!")
		#print(f"\nIf you or someone you know are in need of help, you can refer to SJSU's Behavioral Intervention site(https://www.sjsu.edu/bit/) for help")
		print(f"\nIf you or someone you know is not feeling well, feel free to visit this link:https://www.sjsu.edu/bit/ ")
		print()
		return 1;
	elif results:
		print("\nSorry, I can not answer this because you used an inappropriate word! Please only use words within the context of advising!")
		#print(f"[Inappropriate words: {', '.join(results)}]")
		#print(f"\nPlease use academic/appropriate language!")
		print()
		return 1;		
