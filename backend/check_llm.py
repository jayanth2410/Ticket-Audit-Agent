#testing short description check
#import the llm
from llm import LLM
llm = LLM()

short_description = [
    "User cannot access email on mobile device",
    "Printer is not working in the office",
    "Need help with software installation",]
for desc in short_description:
    result = llm.short_desc_analyser(desc)
    print(f"{desc}: {result}")


#testing resolution_notes_analyser in llm
resolution_notes = [
    "Replaced the faulty hard drive and the issue is resolved.",
    "Provided user with instructions to reset their password. User confirmed it worked.",
    "User was not available for contact. Left a voicemail and sent an email. Awaiting response.",]
for notes in resolution_notes:
    result = llm.resolution_notes_analyser(notes, [])
    print(f"{notes}: {result}")
    