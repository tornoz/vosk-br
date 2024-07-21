#!/usr/bin/env python3

import sys
import argparse
import queue
import sounddevice as sd

import static_ffmpeg
from vosk import KaldiRecognizer

from anaouder.asr.models import load_model, DEFAULT_MODEL
from anaouder.asr.post_processing import post_process_text
from anaouder.text import tokenize, detokenize, load_translation_dict, translate
from anaouder.version import VERSION

import websockets
import asyncio
from googletrans import Translator
import json


def int_or_str(text):
	"""Helper function for argument parsing."""
	try:
		return int(text)
	except ValueError:
		return text


def callback(indata, frames, time, status):
	"""This is called (from a separate thread) for each audio block."""
	if status:
		print(status, file=sys.stderr)
	q.put(bytes(indata))


def format_output(sentence, normalize=False, keep_fillers=False):
	sentence = post_process_text(sentence, normalize, keep_fillers)
	for td in translation_dicts:
		sentence = detokenize( translate(tokenize(sentence), td) )
	return sentence


async def main_mikro(websocket) -> None:
	""" mikro cli entry point """

	global q
	global translation_dicts

	translator = Translator()

	parser = argparse.ArgumentParser(add_help=False)
	parser.add_argument('-l', '--list-devices', action='store_true',
		help='show list of audio devices and exit')
	args, remaining = parser.parse_known_args()
	if args.list_devices:
		print(sd.query_devices())
		parser.exit(0)
	parser = argparse.ArgumentParser(
		description=__doc__,
		formatter_class=argparse.RawDescriptionHelpFormatter,
		parents=[parser])
	parser.add_argument("-o", "--output", type=str, metavar='FILENAME',
		help='text file to store transcriptions')
	parser.add_argument('-m', '--model', type=str, metavar='MODEL_PATH', default=DEFAULT_MODEL,
		help='Path to the model')
	parser.add_argument('-d', '--device', type=int_or_str,
		help='input device (numeric ID or substring)')
	parser.add_argument('-r', '--samplerate', type=int,
		help='sampling rate')
	parser.add_argument('-n', '--normalize', action="store_true",
		help="Normalize numbers")
	parser.add_argument("--translate", nargs='+',
		help="Use additional translation dictionaries")
	parser.add_argument("--keep-fillers", action="store_true",
		help="Keep verbal fillers ('euh', 'beñ', 'alors', 'kwa'...)")
	parser.add_argument("-v", "--version", action="version", version=f"%(prog)s v{VERSION}")
	args = parser.parse_args(remaining)

	# Use static_ffmpeg instead of ffmpeg
	#static_ffmpeg.add_paths()
	
	q = queue.Queue()

	try:
		if args.samplerate is None:
			device_info = sd.query_devices(args.device, 'input')
			# soundfile expects an int, sounddevice provides a float:
			args.samplerate = int(device_info['default_samplerate'])

		model = load_model(args.model)

		translation_dicts = []
		if args.translate:
			translation_dicts = [ load_translation_dict(path) for path in args.translate ]

		if args.output:
			dump_fn = open(args.output, "w")
		else:
			dump_fn = None

		with sd.RawInputStream(samplerate=args.samplerate, blocksize = 1024, device=args.device, dtype='int16',
								channels=1, latency='high', callback=callback):
				print('#' * 80)
				print('Press Ctrl+C to stop the recording')
				print('#' * 80)
				
				rec = KaldiRecognizer(model, args.samplerate)
				
				while True:
					data = q.get()
					if rec.AcceptWaveform(data):
						result = eval(rec.Result())["text"]
						if len(result) > 0:
							output = format_output(result, normalize=args.normalize, keep_fillers=args.keep_fillers)
							print(output)

							# if output is not None and output != "":
								# textfr = translator.translate(output, dest="fr")
								# texten = translator.translate(output, dest="en")
								# textcy = translator.translate(output, dest="cy")
								# textde = translator.translate(output, dest="de")
							await websocket.send(json.dumps({
								"text": output,
								# "textfr": textfr.text,
								# "texten": texten.text,
								# "textcy": textcy.text,
								# "textde": textde.text
							}))
							if dump_fn:
								dump_fn.write(format_output(result, normalize=args.normalize, keep_fillers=args.keep_fillers)+'\n')

	except KeyboardInterrupt:
		print('\nDone')
		parser.exit(0)
	except Exception as e:
		parser.exit(type(e).__name__ + ': ' + str(e))

if __name__ == "__main__":

	start_server = websockets.serve(main_mikro, "localhost", 8000)

	asyncio.get_event_loop().run_until_complete(start_server)
	asyncio.get_event_loop().run_forever()