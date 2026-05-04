import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

def getLinks(site, output="syllabi.txt"):
    try:#error handling
        response = requests.get(site)
        response.raise_for_status()
    except requests.exceptions.RequestException as errors:
        print(f"Error with page: {errors}")
        return
    parsed = BeautifulSoup(response.text, "html.parser")
    links = set()
    for a_tag in parsed.find_all("a", href=True):
        link =  urljoin(site, a_tag["href"])
        links.add(link)
    linksList = sorted(links)#makes sure for no dupes
    return linksList


#spring 2026 links
idtLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Spring&year=2026&program=IDT")
infmLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Spring&year=2026&program=INFM")
infoLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Spring&year=2026&program=INFO")
isdaLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Spring&year=2026&program=ISDA")
maraLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Spring&year=2026&program=MARA")
allLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Spring&year=2026")

#summer 2026 links
idtsLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Summer&year=2026&program=IDT")
infmsLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Summer&year=2026&program=INFM")
infosLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Summer&year=2026&program=INFO")
isdasLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Summer&year=2026&program=ISDA")
marasLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Summer&year=2026&program=MARA")
allsLinks = getLinks("https://ischoolapps.sjsu.edu/gss/ajax/showSheet.php?term=Summer&year=2026")

def linksIndividual():
    with open("syllabi.txt", "w", encoding="utf-8") as writer:
        #spring 2026
        for idt in idtLinks:
            if "syllabus" in idt:
                writer.write(idt + "\n")
        for infm in infmLinks:
            if "syllabus" in infm:
                writer.write(infm + "\n")
        for info in infoLinks:
            if "syllabus" in info:
                writer.write(info + "\n")
        for isda in isdaLinks:
            if "syllabus" in isda:
                writer.write(isda + "\n")
        for mara in maraLinks:
            if "syllabus" in mara:
                writer.write(mara + "\n")
        #summer 2026
        for idts in idtsLinks:
            if "syllabus" in idts:
                writer.write(idts + "\n")
        for infms in infmsLinks:
            if "syllabus" in infms:
                writer.write(infms + "\n")
        for infos in infosLinks:
            if "syllabus" in infos:
                writer.write(infos + "\n")
        for isdas in isdasLinks:
            if "syllabus" in isdas:
                writer.write(isdas + "\n")
        for maras in marasLinks:
            if "syllabus" in maras:
                writer.write(maras + "\n")

def linksAll():
    with open("syllabi.txt", "w", encoding="utf-8") as writer:#uncomment this section and comment other section for all use
        for link in allLinks:
            if "syllabus" in link:
                writer.write(link + "\n")
        for slink in allsLinks:
            if "syllabus" in slink:
                writer.write(slink + "\n")

if __name__ == '__main__':
    linksIndividual()
    #linksAll()#uncomment and comment above for the different uses