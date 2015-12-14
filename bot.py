# -*- coding: utf-8 -*-
from py2neo import Graph, Path, Node, Relationship, authenticate, ServiceRoot
from http.server import BaseHTTPRequestHandler, HTTPServer
from http.client import HTTPSConnection
from urllib.parse import parse_qs, urlencode
import json

import re, os, random, math

from Levenshtein import _levenshtein

PORT = os.environ.get('PORT', 8000)
NEO4J_URL = os.environ.get('NEO4J_URL', 'http://localhost:7474/db/data/')

TARGET_CHANNEL = u'vilma'

SLACK_INCOMING_WEBHOOK_HOST = u'hooks.slack.com'
SLACK_INCOMING_WEBHOOK_PATH = u'<Insert path with token here>'

#Regex pattern to match either words or slack tags like ":simple_smile:" or ":cthulhu:"
word_pattern = re.compile(r'\s*(:[a-zA-Z0-9åäöÅÄÖ_]+:)|([a-zA-Z0-9åäöÅÄÖ]+)')
#Regex pattern to split sentences 
sentence_pattern = re.compile(r'[\.!?]+')

#authenticate("localhost:7474", "neo4j", "password")
#graph = Graph("http://localhost:7474/db/data/")

#authenticate("localhost:7474", "neo4j", "password")
graph = Graph(NEO4J_URL)

#graphenedb_url = os.environ.get("GRAPHENEDB_URL", "http://localhost:7474/")
#graph = ServiceRoot(graphenedb_url).graph

STOP_WORDS = [u"vilma", u"vilman", u"vilmalle", u"vilmasta", u"vilmaa", u"vilmaan", u"ei", u"kyllä", u"olla", u"jonka", u"että", u"jotta", u"koska", u"kuinka", u"jos", u"vaikka", u"kuin", u"kunnes", u"mutta", u"no", u"ehkä", u"ja"]
REPLACEMENTS = {
	u"sinä":u"minä",
	u"sinun":u"minun",
	u"minä":u"sinä",
	u"minun":u"sinun",
	u"meidän":u"teidän",
	u"teidän":u"meidän",
	u"mulle":u"sulle",
	u"sulle":u"mulle",
	u"oon":u"oot",
	u"oot":u"oon",
	u"olet":u"olen",
	u"olen":u"olet",
	u"olemme":u"olette",
	u"olette":u"olemme",
	u"mä":u"sä",
	u"sä":u"mä",
	u"mää":u"sää",
	u"sää":u"mää",
	u"miksi":u"siksi",
	u"milloin":u"silloin",
	u"siksi":u"miksi",
	u"silloin":u"milloin",
	u"miten":u"siten",
	u"sinusta":u"minusta",
	u"minusta":u"sinusta", 
	u"et": u"en",
	u"en": u"et",
	u"ette" : u"emme",
	u"emme": u"ette",
	u"mikä":u"se",
	u"kuka":u"hän",
	u"kenen":u"hänen",
	u"kenestä":u"hänestä",
	u"minne":u"sinne",
	u"missä":u"siellä",
	u"ootte":u"oomme",
	u"oomme":u"ootte",
}


class CypherBuilder:
	def __init__(self, forward=True):
		self.word = False
		self.wordpair = False
		self.group_by_node = False
		self.forward = forward
	def with_word(self, option = True):
		self.word = option
		self.wordpair = not option
		return self
	def with_wordpair(self, option = True):
		self.wordpair = option
		self.word = not option
		return self
	def group(self, option = True):
		self.group_by_node = option
		return self
	def build(self):
		cypher = u"MATCH (a:Wordpair)-[link:Link]->(b:Wordpair)"
		if self.word:
			cypher += u" WHERE {0}.{1}={{word}}".format(u'a' if self.forward else u'b', u'first' if self.forward else u'second')
		elif self.wordpair: 
			cypher += u" WHERE {0}.wordpair={{pair}}".format(u'a' if self.forward else u'b')
		else:
			return None
		if self.group_by_node:
			if self.forward and self.word:
				cypher += u" RETURN a, sum(link.weight) AS weight ORDER BY weight DESC"
			elif self.forward and self.wordpair:
				cypher += u" RETURN b, sum(link.weight) AS weight ORDER BY weight DESC"
			elif self.word:
				cypher += u" RETURN b, sum(link.weight) AS weight ORDER BY weight DESC"
			else: 
				cypher += u" RETURN a, sum(link.weight) AS weight ORDER BY weight DESC"
		else:
			cypher += u" RETURN sum(link.weight) AS weight"
		return cypher


def train_input(message):
	words = word_pattern.findall(message)
	# Flatten the returned tuple (slack_tag, regular_word) into a list 
	words = [(x[0] if x[0] is not None and len(x[0]) > 0 else x[1]) for x in words]
	if len(words) < 2:
		return
	prev = None
	for i in range(0,len(words)-1):
		first_word = words[i].lower()
		second_word = words[i+1].lower()
		if first_word in REPLACEMENTS.keys():
			first_word = REPLACEMENTS[first_word]
		if second_word in REPLACEMENTS.keys():
			second_word = REPLACEMENTS[second_word]	
		pair = u"{0}_{1}".format(first_word, second_word)
		tail_node = graph.merge_one("Wordpair", "wordpair", pair)
		tail_node.properties['first'] = first_word
		tail_node.properties['second'] = second_word
		f = tail_node.properties.get('freq_total', 0)
		if f == 0:
			tail_node.properties['freq_last_word'] = 0
		tail_node.properties['freq_total'] = f + 1
		if i == len(words) - 2:
			#end of sentence 
			tail_node.properties['freq_last_word'] += 1
		tail_node.push()
		if prev is not None:
			(prev_first, prev_second) = prev
			prev_pair = u"{0}_{1}".format(prev_first, prev_second)
			head_node = graph.merge_one("Wordpair", "wordpair", prev_pair)
			head_node.properties['first'] = prev_first
			head_node.properties['second'] = prev_second
			head_node.push()
			cypher = u"""MATCH (a:Wordpair), (b:Wordpair) WHERE a.wordpair={first_pair} AND b.wordpair={second_pair}
			MERGE (a)-[new:Link]->(b) on create set new.weight=0 RETURN new"""
			result = graph.cypher.execute(cypher, {"first_pair": prev_pair, "second_pair": pair})
			result.one.properties['weight'] += 1
			result.one.push()
		prev = (first_word, second_word)

def pick_start_node(first_word, second_word, forward = True):
	builder = CypherBuilder(forward)
	node = None
	#First, try to find a matching wordpair: 
	wordpair = u"{0}_{1}".format(first_word, second_word)
	result = graph.cypher.execute(u"MATCH (a:Wordpair) WHERE a.wordpair={pair} RETURN a", {"pair": wordpair})
	total = result.one
	if total is not None:
		node = total
	if node is None:
		#No match? Try to match reversed pair
		wordpair = u"{0}_{1}".format(second_word, first_word)
		result = graph.cypher.execute(u"MATCH (a:Wordpair) WHERE a.wordpair={pair} RETURN a", {"pair": wordpair})
		total = result.one
		if total is not None:
			node = total
	if node is None:
		#Still no match? Match single word 
		result = graph.cypher.execute(builder.with_word().group().build(), {"word": first_word})
		result_total = graph.cypher.execute(builder.with_word().group(False).build(), {"word": first_word})
		total = result_total.one
		if total is not None and total > 0:
			i = random.randint(1, total)
			for row in result:
				i = i - row.weight
				node = row.a if forward else row.b
				if i <= 0:
					break
	if node is not None:
		return (0, node)
	cypher_random = """MATCH (a:Wordpair) WITH a, rand() AS number RETURN a ORDER BY number LIMIT 10"""
	result_random = graph.cypher.execute(cypher_random)
	distances = []
	for row in result_random:
		d = _levenshtein.distance(row.a.properties['first'], first_word)
		distances.append((d, row.a))
	return sorted(distances, key = lambda x: x[0])[0] 

def recursive_generation(node, alpha, forward = True):
	if random.random() >= alpha:
		return [node['first'], node['second']] if forward else [node['first']]
	pair = u"{0}_{1}".format(node['first'], node['second'])
	builder = CypherBuilder(forward)
	linked_nodes = graph.cypher.execute(builder.with_wordpair().group().build(), {"pair": pair})
	total_weight = graph.cypher.execute(builder.with_wordpair().group(False).build(), {"pair": pair}).one
	if total_weight is None or total_weight < 1:
		return [node['first']]
	fraction = random.random()
	i = fraction * total_weight
	for row in linked_nodes:
		i = i - row.weight
		new_node = row.b if forward else row.a 
		if i <= 0:
			break
	stop_weight = 1.
	#Weight the reduction in alpha (stop criteria) based on how often new node is the last word in a sentence
	freq_total = new_node.properties.get('freq_total', 0)
	freq_last_word = new_node.properties.get('freq_last_word', 0)
	if freq_total > 0:
		stop_weight = freq_last_word / freq_total
	nodes = recursive_generation(new_node, alpha - fraction * stop_weight, forward)
	if forward:
		return [node['first']] + nodes
	return nodes + [node['first']]

def generate_backward(first_word, second_word):
	distance, node = pick_start_node(first_word, second_word, False)
	return (distance, recursive_generation(node, 1., forward = False))

def generate_forward(first_word, second_word):
	distance, node = pick_start_node(first_word, second_word, True)
	return (distance, recursive_generation(node, 1., forward = True))

def generate_replies(message):
	words = word_pattern.findall(message)
	# Flatten the returned tuple (slack_tag, regular_word) into a list 
	words = [(x[0] if x[0] is not None and len(x[0]) > 0 else x[1]) for x in words]
	prev = None
	replies = []
	processed_pairs = []
	for i in range(0,len(words)-1):
		first_word = words[i].lower()
		second_word = words[i+1].lower()
		if (first_word, second_word) in processed_pairs:
			continue
		processed_pairs.append( (first_word, second_word) )
		if first_word in REPLACEMENTS.keys():
			first_word = REPLACEMENTS[first_word]
		if second_word in REPLACEMENTS.keys():
			second_word = REPLACEMENTS[second_word]	
		if first_word in STOP_WORDS or second_word in STOP_WORDS:
			continue
		distance_1, begin = generate_backward(first_word, second_word)
		distance_2, end = generate_forward(first_word, second_word)
		if len(begin) > 0 and len(end) > 0 and begin[-1] == end[0]:
			begin = begin[0:-1]
		replies.append( (distance_1 + distance_2, u" ".join(begin + end) ) )
	return replies

def generate_random_reply():
	result = graph.cypher.execute("MATCH (a:Wordpair) WITH a, rand() AS number RETURN a ORDER BY number LIMIT 1")
	begin = recursive_generation(result.one, 1., forward = False)
	end = recursive_generation(result.one, 1., forward = True)
	if len(begin) > 0 and len(end) > 0 and begin[-1] == end[0]:
		begin = begin[0:-1]
	return u" ".join(begin + end)

def compute_entropy(reply):
	entropy = 0.
	words = reply.split(" ")
	for i in range(0,len(words)-1):
		pair = u"{0}_{1}".format(words[i], words[i+1])
		cypher_total_weights = """MATCH (:Wordpair)-[link:Link]->(a:Wordpair) WHERE a.wordpair={pair} RETURN sum(link.weight) AS weight"""
		result_total = graph.cypher.execute(cypher_total_weights, {"pair": pair})
		total = result_total.one
		if total is not None and total > 0:
			inverse = 100 - total
			if inverse < 1:
				inverse = 1
			entropy += math.log(inverse)
	return entropy

def extract_postvars(postvars, *args):
	vals = []
	for x in args:
		val = postvars.get(x, None)
		if val is None:
			val = postvars.get(x.decode('utf-8'), None)
		vals.append(val)
	return vals

class RequestHandler(BaseHTTPRequestHandler):
	def do_POST(self): 
		content_len = int(self.headers.get('content-length',0))
		post_body = self.rfile.read(content_len)
		postvars = parse_qs(post_body.decode('ASCII'))
		(token, msg, username, channel_name, train) = extract_postvars(postvars, b'token', b'text', b'user_name', b'channel_name', b'train')
		self.send_response(200)
		self.end_headers()
		if username is not None and (username[0] == u'VILMA' or username[0] == u'slackbot'):
			return
		message = msg[0]
		all_replies = [] 
		for sentence in sentence_pattern.split(message):
			if len(sentence) < 1:
				continue
			replies = []
			selected = None
			replies = generate_replies(message)
			if len(replies) > 0:
				entropies = [(reply, compute_entropy(reply) / (distance + 1) ) for distance, reply in replies]
				entropies = sorted(entropies, key = lambda x: -x[1])
				total_entropy = 0.
				for (reply, entropy) in entropies:
					total_entropy += entropy
				i = random.uniform(0, total_entropy)
				selected = None
				for (reply, entropy) in entropies:
					selected = reply
					i -= entropy
					if i <= 0:
						break
				if selected is None:
					selected = entropies[0][0]
			if selected is None:
				selected = generate_random_reply()
			all_replies.append(selected)
			if not train:
				payload = { "text" : selected, "username": u"VILMA", "channel": u"#{0}".format(TARGET_CHANNEL), "icon_url": u"https://i1.wp.com/www.vincit.fi/wordpress/wp-content/uploads/2015/04/roboduck05.png" }
				connection = HTTPSConnection(SLACK_INCOMING_WEBHOOK_HOST)
				connection.request("POST", SLACK_INCOMING_WEBHOOK_PATH, json.dumps(payload))
				response = connection.getresponse()
			else:
				self.wfile.write(u'{{"message": "{0}"}}'.format(selected).encode('utf-8'))
		for sentence in sentence_pattern.split(message):
			train_input(sentence)
handler_class = RequestHandler
int_port = int(PORT)
server_address = ('', int_port)
httpd = HTTPServer(server_address, handler_class)
httpd.serve_forever()
