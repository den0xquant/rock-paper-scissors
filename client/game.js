import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { User, Bot, RotateCcw, Hand, Trophy } from "lucide-react";

const choices = [
    { name: "rock", emoji: "🪨" },
    { name: "paper", emoji: "📄" },
    { name: "scissors", emoji: "✂️" },
];

export default function Game() {
    const [playerScore, setPlayerScore] = useState(0);
    const [computerScore, setComputerScore] = useState(0);
    const [playerChoice, setPlayerChoice] = useState(null);
    const [computerChoice, setComputerChoice] = useState(null);
    const [result, setResult] = useState(null); // 'win', 'lose', 'tie'
    const [isAnimating, setIsAnimating] = useState(false);

    const handlePlay = (choice) => {
        if (isAnimating) return;

        setIsAnimating(true);
        const computerChoice = choices[Math.floor(Math.random() * choices.length)];
        setPlayerChoice(choice);
        setComputerChoice(computerChoice);

        // Determine winner
        if (choice.name === computerChoice.name) {
            setResult("tie");
        } else if (
            (choice.name === "rock" && computerChoice.name === "scissors") ||
            (choice.name === "paper" && computerChoice.name === "rock") ||
            (choice.name === "scissors" && computerChoice.name === "paper")
        ) {
            setResult("win");
            setPlayerScore((prev) => prev + 1);
        } else {
            setResult("lose");
            setComputerScore((prev) => prev + 1);
        }

        setTimeout(() => {
            setIsAnimating(false);
        }, 1500)
    };

    const resetGame = () => {
        setPlayerScore(0);
        setComputerScore(0);
        setPlayerChoice(null);
        setComputerChoice(null);
        setResult(null);
        setIsAnimating(false);
    }

    const getResultMessage = () => {
        switch (result) {
            case 'win': return 'You Win!';
            case 'lose': return 'You Lose!';
            case 'tie': return "It's a Tie!";
            default: return 'Choose your move!';
        }
    }

    return (
        <div className="min-h-screen bg-gray-100 flex flex-col items-center justify-center p-4">
            <Card className="w-full max-w-md mx-auto shadow-lg">
                <CardContent className="p-6">
                    {/* Header */}
                    <div className="text-center mb-6">
                        <h1 className="text-4xl font-bold text-gray-800">Rock Paper Scissors</h1>
                    </div>

                    {/* Scoreboard */}
                    <div className="flex justify-around items-center mb-6 p-4 bg-gray-50 rounded-lg">
                        <div className="text-center">
                            <div className="flex items-center justify-center gap-2 text-blue-600">
                                <User />
                                <span className="font-semibold">You</span>
                            </div>
                            <p className="text-3xl font-bold">{playerScore}</p>
                        </div>
                        <div className="text-2xl font-bold text-gray-400">VS</div>
                        <div className="text-center">
                            <div className="flex items-center justify-center gap-2 text-purple-600">
                                <Bot />
                                <span className="font-semibold">CPU</span>
                            </div>
                            <p className="text-3xl font-bold">{computerScore}</p>
                        </div>
                    </div>

                    {/* Result Display */}
                    <div className="h-28 flex flex-col items-center justify-center text-center mb-6">
                        <AnimatePresence mode="wait">
                            {playerChoice ? (
                                <motion.div
                                    key="result"
                                    initial={{ opacity: 0, y: 20 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    exit={{ opacity: 0, y: -20 }}
                                    className="w-full"
                                >
                                    <div className="flex justify-around items-center text-5xl">
                                        <motion.div animate={{ rotate: [0, -10, 10, 0] }}>{playerChoice.emoji}</motion.div>
                                        <div className="text-xl text-gray-500">vs</div>
                                        <motion.div animate={{ rotate: [0, 10, -10, 0] }}>{computerChoice.emoji}</motion.div>
                                    </div>
                                    <p className={`mt-3 text-xl font-bold ${result === 'win' ? 'text-green-500' : result === 'lose' ? 'text-red-500' : 'text-yellow-500'}`}>
                                        {getResultMessage()}
                                    </p>
                                </motion.div>
                            ) : (
                                <motion.div
                                    key="prompt"
                                    initial={{ opacity: 0 }}
                                    animate={{ opacity: 1 }}
                                    className="flex items-center gap-2 text-gray-500"
                                >
                                    <Hand className="w-5 h-5" />
                                    <p className="text-lg font-medium">{getResultMessage()}</p>
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </div>

                    {/* Player Controls */}
                    <div className="flex justify-center gap-4 mb-6">
                        {choices.map((choice) => (
                            <Button
                                key={choice.name}
                                onClick={() => handlePlay(choice)}
                                disabled={isAnimating}
                                size="lg"
                                className="w-24 h-24 text-4xl rounded-full shadow-md transition-transform transform hover:scale-105 active:scale-95 disabled:opacity-50"
                            >
                                {choice.emoji}
                            </Button>
                        ))}
                    </div>

                    {/* Reset Button */}
                    <div className="text-center">
                        <Button variant="outline" onClick={resetGame}>
                            <RotateCcw className="w-4 h-4 mr-2" />
                            Reset Game
                        </Button>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}